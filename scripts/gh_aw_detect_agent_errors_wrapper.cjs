#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const DEFAULT_LOG_FILE = "/tmp/gh-aw/agent-stdio.log";
const UPSTREAM_DETECTOR = path.join(
  __dirname,
  "detect_agent_errors.upstream.cjs",
);

const HARNESS_ATTEMPT_PATTERN =
  /^\[([a-z0-9_-]+-harness)\].*\battempt (\d+):/i;
const HARNESS_SUCCESS_PATTERN =
  /^\[([a-z0-9_-]+-harness)\].*\bsuccess on attempt (\d+):/i;
const HARNESS_DONE_PATTERN =
  /^\[([a-z0-9_-]+-harness)\].*\bdone: exitCode=0\b/i;
const PROCESS_SIGNAL_PATTERN =
  /\bprocess (?:exit event|closed)\b.*\bsignal=(SIG(?:TERM|KILL|INT))\b/;

/**
 * Hide timeout-like signals from attempts that the harness demonstrably
 * recovered. gh-aw v0.80.9 scans the complete combined transcript and treats
 * any process signal as an overall timeout, even when a later attempt exits 0.
 *
 * Require both "success on attempt N" and the final "done: exitCode=0" marker.
 * Signals from attempt N or later, and every signal in an unsuccessful run,
 * remain untouched for the upstream detector.
 */
function normalizeRecoveredAttemptSignals(logContent) {
  const lines = logContent.split(/(?<=\n)/);
  let recovered = null;

  for (let index = 0; index < lines.length; index += 1) {
    const success = lines[index].match(HARNESS_SUCCESS_PATTERN);
    if (!success) continue;

    const harness = success[1];
    const attempt = Number(success[2]);
    const hasDone = lines
      .slice(index + 1)
      .some((line) => {
        const done = line.match(HARNESS_DONE_PATTERN);
        return done && done[1] === harness;
      });

    if (hasDone) recovered = { harness, attempt, successIndex: index };
  }

  if (!recovered) return logContent;

  return lines
    .map((line, index) => {
      const attempt = line.match(HARNESS_ATTEMPT_PATTERN);
      if (
        index >= recovered.successIndex ||
        !attempt ||
        attempt[1] !== recovered.harness ||
        Number(attempt[2]) >= recovered.attempt ||
        !PROCESS_SIGNAL_PATTERN.test(line)
      ) {
        return line;
      }

      return line.replace(/\bsignal=/g, "recovered_termination=");
    })
    .join("");
}

function runUpstreamDetector() {
  const logFile = process.env.GH_AW_AGENT_STDIO_LOG || DEFAULT_LOG_FILE;
  const original = fs.readFileSync(logFile, "utf8");
  const normalized = normalizeRecoveredAttemptSignals(original);

  if (!fs.existsSync(UPSTREAM_DETECTOR)) {
    throw new Error(`Upstream gh-aw detector not found: ${UPSTREAM_DETECTOR}`);
  }

  try {
    if (normalized !== original) fs.writeFileSync(logFile, normalized);

    const result = spawnSync(process.execPath, [UPSTREAM_DETECTOR], {
      env: process.env,
      stdio: "inherit",
    });

    if (result.error) throw result.error;
    return result.status === null ? 1 : result.status;
  } finally {
    if (normalized !== original) fs.writeFileSync(logFile, original);
  }
}

if (require.main === module) {
  if (process.argv[2] === "--normalize") {
    process.stdout.write(
      normalizeRecoveredAttemptSignals(fs.readFileSync(0, "utf8")),
    );
  } else {
    process.exitCode = runUpstreamDetector();
  }
}

module.exports = { normalizeRecoveredAttemptSignals, runUpstreamDetector };
