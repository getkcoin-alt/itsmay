# Continuity Operator Checklist

Before calling Scrappy continuity recoverable, verify the following with real evidence.

- [ ] Continuity branch CI is green at the exact commit being reviewed.
- [ ] Production archive endpoints are deployed only after explicit operator approval.
- [ ] A real encrypted `.enc` backup is created on the operator's machine.
- [ ] `scrappy-continuity verify-encrypted` passes on that file.
- [ ] The encrypted file SHA-256 is recorded separately.
- [ ] A ChatGPT `conversations.json` export is included if the operator wants historical chat continuity.
- [ ] No plaintext conversation archive is committed to public Git.
- [ ] At least one independent encrypted secondary copy exists.
- [ ] At least one offline encrypted copy exists.
- [ ] A clean restore target accepts a dry run.
- [ ] The clean restore target accepts an explicit apply after review.
- [ ] A fresh model/provider can answer capsule continuity evals without original account memory.
- [ ] Unsupported history is reported as unknown rather than invented.

**Never fake the magic. Build the mechanism until reality feels magical.**
