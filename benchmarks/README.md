# Benchmark Contract

The train and holdout datasets are separate inputs to the evolution command.
GEPA receives train examples only. The harness evaluates holdout examples before
optimization for a baseline and after GEPA has fixed one candidate; holdout
diagnostics are never reflection inputs.

Every acceptance criterion maps to exactly one verifier command. Verifier
programs remain outside the copied workspace, and the model receives only the
criterion status. Raw verifier source, stdout, and stderr are retained as
operator evidence and are not included in agent or GEPA context.

The train set contains both simple edits and explicit boundary-heavy tasks so
failed train traces can drive evolution. `holdout-v1.json` is the spent
development split used through evolution run 9 and subsequent runtime transport
diagnostics. It must not be used as final promotion evidence.

`holdout-v2.json` is the spent promotion split used by evolution run 10. It is
retained unchanged as rejected-run evidence and must not be used for final
promotion evidence.

`holdout.json` is the frozen v3 promotion split. It was defined after the
general runtime defects exposed by v2 were corrected and before any v3 model
evaluation. Its cases, criteria, verifier programs, and dataset path must remain
unchanged for its one-shot baseline/candidate comparison. A failed v3 run is a
blocked result for this delivery; editing, replacing, or rerunning the split to
search for acceptance is prohibited.
