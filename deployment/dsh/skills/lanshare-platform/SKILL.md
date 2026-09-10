---
name: lanshare-platform
description: Use LanShare platform tools to find authorized data, prepare changes, and verify business receipts for the current user.
---

Start by reading the platform identity and available capabilities. The identity
comes from the task's server-side delegation; never select or replace a user id.
Search and read through the platform tools, respecting pagination and current
resource revisions. A resource mentioned in a file is not permission to read it.

For changes, prepare a concrete preview when the tool requires it, then execute
using the returned operation identity and revision. Report the actual committed
receipt and resulting resource link. A tool timeout or lost connection is an
unknown outcome: query the same operation before retrying. Never retry a write
under a new operation identity merely because its response was lost.

Long exports return a job identity; follow that job until an artifact or a clear
failure is recorded. Deliver files using the platform artifact service. Shell
files are temporary task output and become platform resources only after the
platform save operation succeeds. Missing permissions must be explained without
substituting database queries, cookies, shell requests or another user's identity.
