# VM Auto Test

Use this skill to guide authorized local VMware lab validation with `vm-auto-test`.

## Safety boundary

- Only run against local VMware VMs and samples the user is authorized to test.
- Do not create, modify, recommend, or obfuscate attack samples.
- Do not provide bypass or detection-evasion instructions.
- Keep guest credentials in `.env` or prompt input; never write real passwords to YAML, README, reports, or commits.

## Workflow

1. Load an isolated environment file:

   ```bash
   vm-auto-test --env-file .env vms
   ```

2. Create and run a baseline config first:

   ```bash
   vm-auto-test --env-file .env init-config --output configs/baseline.yaml --mode baseline
   vm-auto-test --env-file .env run-config configs/baseline.yaml
   ```

3. Continue to AV mode only when baseline returns `BASELINE_VALID`.

4. Run AV config with a valid baseline `result.json` path:

   ```bash
   vm-auto-test --env-file .env init-config --output configs/av.yaml --mode av
   vm-auto-test --env-file .env run-config configs/av.yaml
   ```

## Report interpretation

- `BASELINE_VALID`: sample and verification command produced an observable effect.
- `BASELINE_INVALID`: no observable effect; check the sample path/command or verification command.
- `AV_NOT_BLOCKED`: AV-mode verification observed the effect.
- `AV_BLOCKED_OR_NO_CHANGE`: AV-mode verification did not observe the effect; with a valid baseline, this is a blocking/no-change indication.

For schema v2 batch reports, read the top-level `summary` and then inspect each `samples/<id>/result.json` for details.
