# 0.6.4 Windows readiness qualification

Status: candidate. The native build and scoped unit checks passed; installed
full-setup permission effects and current platform transport checks are pending.
No v0.6.4 stable release has been created.

The native installer manifest uses ready to mean the immutable archive is
available for explicit local candidate installation and its complete file map
has been validated. It is not a runtime qualification or publication verdict.
Use the exact local archive until the formal release exists.

This version updates the Windows critical-read readiness check for the native
provisioner's actual target accounts and known normal groups. It retains DENY
ACEs, leaves the global ACL grant implementation unchanged, and does not replace
OS access checks, tool execution, approval, or sandbox policy. Native execution
continues to enforce the real Windows permissions.

The first native grant-fixture assertion and the first DENY snapshot test
failed and are retained. The fixture was corrected to require an explicit grant;
the snapshot uses a read-control security API so it can observe the intended
DENY. The final six registered tests contain eighteen DENY branches and four
negative controls. Counts and conditions are reported separately.

Windows native packages are v0.6.4; macOS/Linux reuse the unchanged v0.6.2 native
assets and original recipes. The common Python distribution is v0.6.4. Existing
v0.6.3 response identity checks remain unchanged except the package version.
Current evidence will be added here before stable publication; old native and
complex-task results are not relabeled as new executions.
