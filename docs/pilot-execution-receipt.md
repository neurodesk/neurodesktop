# Pilot execution receipt

The pilot execution receipt is Neurodesktop's supplemental, machine-readable
record for a provisional module pilot. It binds the scientific contract,
retained code, module command surface, inputs, in-image Slurm evidence,
Lightcone artifacts, outputs, and exported Workflow Run RO-Crate without
claiming the actual tool-container identity.

The immutable v1.0.0 document contract is
[`schemas/neurodesktop-pilot-execution-receipt-v1.0.0.schema.json`](../schemas/neurodesktop-pilot-execution-receipt-v1.0.0.schema.json).
Illustrative success and timeout receipts plus negative scenarios live under
[`tests/fixtures/pilot-execution-receipts/`](../tests/fixtures/pilot-execution-receipts/).
Their hashes and paths exercise the document contract; they are not captured
pilot-run evidence.

The built image installs the public CLI as
`/usr/local/bin/neurodesktop-pilot-receipt` and the immutable schema under
`/opt/neurodesktop/schemas/`.

## Command-line interface

Validate a finalized receipt against its persistent workspace and one or more
read-only module roots:

```bash
neurodesktop-pilot-receipt validate receipt.json \
  --workspace-root /neurodesktop-storage/astra-lightcone-demo \
  --allowed-module-root /cvmfs/neurodesk.ardc.edu.au
```

Generate a final receipt from post-job source facts:

```bash
neurodesktop-pilot-receipt generate candidate.json \
  --receipt-directory /neurodesktop-storage/astra-lightcone-demo/runs/RUN_ID/receipt \
  --workspace-root /neurodesktop-storage/astra-lightcone-demo \
  --allowed-module-root /cvmfs/neurodesk.ardc.edu.au
```

The candidate has the final receipt's field layout, except that the generator
owns and derives the following fields:

- the complete `trust` and `generation` objects;
- every workspace artifact and resolved module file's `sha256` and
  `sizeBytes`;
- each universe's RFC 8785 `canonicalSha256`;
- each verification result's `manifestSha256`; and
- the RO-Crate inventory bytes and `treeSha256`.

The caller supplies scientific, scheduler, and Lightcone facts plus confined
paths. The generator does not repair contradictory facts. It derives the
fields above, validates the complete temporary receipt, and publishes only a
fully valid final document. Both commands return exit status 2 and an
`invalid:` diagnostic for an untrusted document or filesystem state.

## Evidence groups

| Group | Required evidence |
| --- | --- |
| Receipt | Exact schema version, receipt ID, timestamps, workspace root, outcome, and atomic-generation marker |
| Trust | Amber or spec-only state, separate output-integrity result, and the mandatory statement that actual container identity is unattested |
| Analysis | ASTRA specification bytes plus every selected universe definition, resolved decisions, and canonical semantic hash |
| Execution | Retained Slurm script and exactly one versioned module, including modulefile, wrapper, command names, resolved paths, hashes, and sizes |
| Inputs | Every consumed file with source URI, source snapshot, confined workspace path, byte size, and SHA-256 |
| Slurm | Submission arguments, requested resources, job ID, terminal state and exit code, timings, node, logs, `scontrol`, and `sacct` evidence |
| Lightcone | Exact version and `runtime: none`, saved status, every canonical per-output manifest, and fresh coverage-checked verification results |
| Outputs | Each metric, derivative image, and visual-QC file with output/universe identity, media type, path, size, and SHA-256 |
| RO-Crate | Confined crate root, metadata identity, a hashed file inventory, and an aggregate tree SHA-256 |

There is one `execution.module` object rather than a module array. Version 1
therefore cannot silently expand from the agreed single-module recipe into a
multi-module execution.

## Outcome and trust rules

A `succeeded` receipt requires all of the following at schema level:

- Slurm state `COMPLETED` with exit code `0:0`;
- persistent trust level `executed-unverified`;
- a passed output-integrity result;
- a saved Lightcone status snapshot and at least one canonical manifest;
- fresh verification marked passed with complete, non-empty output coverage;
- one or more hashed outputs; and
- a complete, inventoried RO-Crate.

The `failed`, `cancelled`, and `timed-out` outcomes cannot claim passed output
integrity or a completed Slurm state. `cancelled` normalizes the terminal Slurm
state to `CANCELLED`; `timed-out` normalizes it to `TIMEOUT`. A failed attempt
without a canonical successful-output record remains `spec-only`. If partial
manifests exist, the semantic validator may classify it as
`executed-unverified`, but never as a verified run.

Every v1 receipt fixes `actualContainerIdentity` to `null`,
`actualContainerIdentityAttested` to `false`, and the statement to:

> The actual tool-container identity is not attested by this receipt.

The module wrapper's container reference is diagnostic evidence only. It must
not be promoted to actual container identity.

## Hashing

- `sha256` fields contain lowercase SHA-256 of the referenced file's exact
  bytes; `sizeBytes` is the same file's byte length.
- A universe `canonicalSha256` is SHA-256 of the RFC 8785 JSON Canonicalization
  Scheme encoding of `{"universeId": ..., "decisions": ...}`. The original
  universe file is hashed separately as `definition`.
- The RO-Crate inventory lists every regular file below `rootRelativePath` as
  `{relativePath, sizeBytes, sha256}`, sorted by UTF-8 relative-path bytes.
  `treeSha256` is SHA-256 of the RFC 8785 encoding of that ordered array. The
  inventory artifact itself is also byte-hashed normally.
- Lightcone verification result `manifestSha256` must equal the byte hash of
  the manifest referenced by the same output key.

Passing JSON Schema validation does not recompute any of these values and is
not an authenticity or provenance claim.

## Path confinement

All `relativePath` values are relative to `workspaceRoot`; absolute paths,
empty segments, and `.` or `..` segments are rejected by the schema. The
The CLI semantic validator must additionally:

1. require the configured workspace root to equal the receipt's root;
2. resolve every existing component and reject a path whose final target or
   any symlink target escapes that root;
3. open and hash the confined target rather than trusting a path string;
4. accept modulefile, wrapper, and command-surface absolute paths only under
   configured read-only Neurodesk module/container roots; and
5. reject missing files, special files, unresolved links, duplicate targets,
   shell expansion, or a hash/size mismatch.

The v1 schema deliberately cannot express filesystem or symlink checks. The
CLI resolves and hashes the named regular files and rejects missing targets,
escapes, special files, and duplicate workspace targets.

## Semantic validation

After schema and path validation, the receipt validator fails closed unless:

- `exactSpecifier` equals `name + "/" + version` and every command actually
  resolves through the recorded module command surface;
- universe IDs, input IDs, output keys, manifest keys, and verification keys
  are unique and refer to the selected ASTRA specification;
- the output, manifest, expected-output, and verification-result key sets are
  identical for a successful receipt;
- each Lightcone manifest has schema version 1 and matching output/universe
  IDs, and its recorded Slurm job ID matches the receipt;
- fresh verification contains exactly one passing result for every expected
  recipe-backed output;
- `scontrol`, `sacct`, stdout, and stderr all identify the recorded job and
  agree with its normalized terminal state, exit code, and timestamps;
- the RO-Crate inventory and tree hash cover the exported crate and its
  embedded specification, universes, manifests, and declared output members;
  and
- trust is derived from the reconciled evidence rather than accepted from the
  receipt's label alone.

Unknown fields, unknown schema versions, missing evidence, inconsistent facts,
or partial success evidence invalidate the receipt. Consumers must not fall
back to an older or incomplete interpretation.

For Lightcone 0.4.0, the validator parses each canonical schema-v1 manifest,
the saved unversioned status snapshot, and Neurodesktop's supplemental fresh
verification JSON. A successful receipt requires identical output-key sets,
`ok` status for every output, exactly one passing verification result per
manifest, and matching manifest hashes. The validator does not reinterpret an
`lc verify` exit code as evidence.

For Slurm, the retained script must be the target of the recorded
`sbatch --parsable` argv. `scontrol` and `sacct` must agree with the receipt's
job ID, partition, requested resources, normalized state, node, timestamps,
and elapsed duration. The terminal control record is authoritative for the
exit code. Slurm legitimately renders an allocation cancellation as, for
example, `CANCELLED by 1000` with allocation exit `0:0` while `scontrol`
records `CANCELLED` with the terminating signal; the validator accepts only
that narrow `CANCELLED`/`TIMEOUT` allocation normalization and remains exact
for successful and ordinary failed jobs. Manifest and verification completion
must fall inside the allocation, and receipt creation/finalization must follow
terminal state.

## Atomic publication

The generator writes a complete receipt only after Slurm reaches a terminal
state:

1. create a uniquely named temporary file in the final receipt directory;
2. serialize UTF-8 JSON, flush it, and `fsync` the file;
3. validate the temporary document against this schema and the semantic rules;
4. make the temporary file read-only;
5. atomically rename it to `receipt.json` without replacing an existing final
   receipt; and
6. `fsync` the parent directory.

The implementation uses Linux `renameat2(RENAME_NOREPLACE)` or macOS
`renamex_np(RENAME_EXCL)` and fails closed on platforms without an atomic
no-replace rename primitive.

Readers ignore temporary files and consume only `receipt.json`. Generation
failure leaves no final receipt. Atomic publication prevents partial reads; it
does not sign or authenticate the receipt, which is another reason the module
pilot remains amber.

## Versioning

The schema filename, `$id`, and `schemaVersion` all carry `1.0.0`. This file is
immutable once published. Any field or invariant change requires a new
semantic version and a new schema file; consumers must select an exact
supported version and fail closed on every other value.
