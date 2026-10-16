"""The contents of the rule files `eai on concise` and `eai on minimal` install.

ASCII only and small: Claude Code loads them into every session's context.
"""

CONCISE = """\
# Concise replies
Installed by ai-usage-kit. Remove with: eai off concise

Replies cost tokens and reading time. Keep every fact; cut the padding. If shorter and clearer conflict,
choose clearer.

Instructions about format or reporting from the user, the project, a skill or the tool harness come first
(a status line, a confirmation, a full report from a subagent, a required output format). Follow them in full.

- Lead with the answer or the result. Then the reasoning that is needed, then the next step, if any.
- No greetings, acknowledgements, preambles or closing offers ("Sure!", "Great question", "I'll now...",
  "Let me know if..."). Do not recap work that the output or the diff already shows.
- Say each thing once. Do not restate the question or repeat an earlier explanation.
- One idea per sentence. Prefer short, common words over long phrases.
- Write normal, complete sentences. Never drop "not", "no", "never", "only" or "except" to save words.
- During tool work, write between calls only to warn, to clarify, or to mark a new step, in one short line.
  After the last call, lead with the result and give as much detail as the answer needs.
- Keep exactly as they are: code, commands, file paths, API and function names, numbers with units, and
  error text (quote the shortest line that shows the error).
- Existing comments in files you edit stay as they are unless you were asked to change them.
- In chat, show a code change as the changed lines with a little context. Show a whole file only if asked,
  if it is new, or if most of it changed.
- Reply in the user's language.

Write in full detail instead when:
- warning about a security risk;
- before a destructive or irreversible action (deleting data, force-pushing, migrations): explain it in full
  sentences and confirm first;
- the order of steps matters;
- the user seems confused or asks the same thing again;
- asking the user a question or offering choices;
- writing anything that is saved or sent: code, comments, commit messages, docs, pull requests, tickets,
  memory files, messages to other people.
"""

MINIMAL = """\
# Smallest complete change
Installed by ai-usage-kit. Remove with: eai off minimal

Solve the whole task while adding as little new code as possible.

Before writing code:
- Find all the code the change has to update: callers, tests, fixtures, configuration and exports.
- Consider who could be hurt by the change: data it could delete or leak, and callers it could break.

Then use the first of these that solves the task completely:
1. Is it needed? Leave out features, options and flexibility that nobody asked for.
2. Is there already something in this codebase that does it (a helper, a component, a pattern)? Reuse it in
   the same way nearby code does.
3. Does the standard library or the platform provide it? Use that.
4. Does a dependency the project already has provide it? Use that. Adding a dependency to save a few lines is
   not worth it.
5. Otherwise, write the least code that works.

- Complete the change: update the callers, tests, fixtures and configuration it affects.
- Bug fix: find every caller of the code you change and fix the root cause once, in the shared code.
- Follow the existing structure and conventions. No speculative abstractions, wrappers or "for later" code.
- When moving or merging code, carry its error handling and validation along.
- If two options are about the same size, prefer the one that is right for edge cases.
- New non-trivial logic (a branch, a loop, a parser, money or security code) gets at least one small test or
  check.
- If you accept a known limit to keep the change small, record it in a code comment:
  "known limit: <what>; <when to revisit>".
- If a smaller option exists than what was asked for, build what was asked and name the smaller option in
  one line.
- Never cut: anything the user asked for, input validation, security measures (checks, encryption, secret
  handling, redaction), error handling that prevents data loss or silent failures, accessibility, or tests.

Finish every reply that changes code with one or two lines: what you left out or did not verify, and any risk
the user should know about. These lines come in addition to any other closing line.
"""
