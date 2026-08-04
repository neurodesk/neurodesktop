import { spawn } from "node:child_process"

const DEFAULT_PLUGIN_ROOT =
  "/opt/neurodesktop/agent-skills/plugins/reproduction"

function hookRoot() {
  return process.env.NEURODESKTOP_LIGHTCONE_PLUGIN_ROOT || DEFAULT_PLUGIN_ROOT
}

function additionalContext(stdout) {
  try {
    const payload = JSON.parse(stdout)
    const context = payload?.hookSpecificOutput?.additionalContext
    return typeof context === "string" ? context.trim() : ""
  } catch {
    return ""
  }
}

function runHook(script, payload, cwd, timeoutMs) {
  const root = hookRoot()
  return new Promise((resolve) => {
    const child = spawn("bash", [`${root}/hooks/scripts/${script}`], {
      cwd,
      env: {
        ...process.env,
        CLAUDE_PLUGIN_ROOT: root,
        PLUGIN_ROOT: root,
      },
      stdio: ["pipe", "pipe", "ignore"],
    })
    let stdout = ""
    const timer = setTimeout(() => child.kill(), timeoutMs)

    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk) => {
      stdout += chunk
    })
    child.on("error", () => {
      clearTimeout(timer)
      resolve("")
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      resolve(code === 0 ? additionalContext(stdout) : "")
    })
    child.stdin.end(JSON.stringify(payload))
  })
}

function toolPayload(input, output, directory) {
  const filePath = input.args?.filePath || output.metadata?.filepath
  return {
    cwd: directory,
    session_id: input.sessionID,
    tool_name: input.tool,
    tool_input: {
      ...input.args,
      file_path: filePath,
    },
    tool_response: {
      filePath,
    },
  }
}

function appendContext(output, context) {
  if (!context) return
  const current = typeof output.output === "string" ? output.output : ""
  output.output = current ? `${current}\n\n${context}` : context
}

export const LightconeHooks = async ({ directory }) => {
  // OpenCode has no SessionStart context channel. Its system transform is the
  // durable equivalent: the upstream hook runs once when the plugin loads and
  // its result remains visible to the model throughout the session.
  const sessionContext = await runHook(
    "astra-session-start.sh",
    { cwd: directory },
    directory,
    15_000,
  )

  return {
    "experimental.chat.system.transform": async (_input, output) => {
      if (sessionContext) output.system.push(sessionContext)
    },
    "tool.execute.after": async (input, output) => {
      const script =
        input.tool === "read"
          ? "activate-on-read.sh"
          : input.tool === "write" || input.tool === "edit"
            ? "validate-on-save.sh"
            : undefined
      if (!script) return

      const context = await runHook(
        script,
        toolPayload(input, output, directory),
        directory,
        script === "activate-on-read.sh" ? 5_000 : 30_000,
      )
      appendContext(output, context)
    },
  }
}
