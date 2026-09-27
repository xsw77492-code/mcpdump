# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability.

Report it privately to the maintainers via GitHub's private reporting, or by
opening a draft security advisory. Describe the affected component, the impact,
and a minimal reproduction if you have one. You will receive an acknowledgement,
and a fix will be prepared before any public disclosure.

## Scope

`mcpdump` is a **client-side debugging tool**. It inspects, calls and proxies MCP
servers; it does not itself expose a network service. The relevant threat model is
therefore:

- **A malicious or compromised MCP server** feeding `mcpdump` crafted JSON-RPC
  frames, oversized payloads, or unexpected field types.
- **A malicious recording** (a `.jsonl` file) passed to `replay`, `diff` or `mock`.
- **A malicious client configuration** scanned by `discover`.

Treat a recording file the same way you would treat untrusted input. `replay`,
`diff` and `mock` parse it; a recording is designed to be shared, but you should
still not open one from a source you do not trust.

## What mcpdump does to limit exposure

- `record`/`replay`/`diff` parse JSON line by line and validate field types
  explicitly rather than trusting the file shape.
- `discover` reports **variable names** and whether they are set — never the
  values — because client config files routinely contain secrets
  (`GITHUB_TOKEN`, etc.).
- `watch` proxies a server without rewriting its frames, so a proxy mode cannot
  accidentally introduce a silent data path.

## Supported versions

| Version | Supported |
|---|---|
| latest release | :white_check_mark: |

Only the most recent release receives security fixes. Upgrade before reporting a
bug against an older version.
