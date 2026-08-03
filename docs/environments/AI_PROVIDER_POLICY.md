# AI provider policy

Search and chat calls are guarded independently from email, webhooks, and
other external side effects.

`EXTERNAL_AI_MODE` is optional and takes precedence over
`EXTERNAL_SIDE_EFFECTS_MODE` for calls made through `core.ai_guard`:

- `enabled` uses the configured OpenAI provider and requires
  `OPENAI_API_KEY` at call time.
- `sandbox` returns deterministic responses only in development and test.
- `disabled` fails closed with `ExternalAIBlocked`.

Review, staging, and production are reachable deployment environments. They
therefore reject the sandbox provider even if it is selected explicitly or
inherited from the broad side-effect policy. A missing key never downgrades
`enabled` to sandbox.

| APP_ENV | enabled | sandbox | disabled |
| --- | --- | --- | --- |
| development | Real provider; key required | Deterministic provider allowed | Blocked |
| test | Real provider; key required | Deterministic provider allowed | Blocked |
| review | Real provider; key required | Refused | Blocked |
| staging | Real provider; key required | Refused | Blocked |
| production | Real provider; key required | Refused | Blocked |

Disposable signed-lifecycle certification may retain a staging identity while
using deterministic providers only when both the standard `CI` marker and the
existing `PDFSEARCH_TEST_EMBEDDINGS` marker are explicit. Those markers must
never be present in a real deployment.

If the variable is omitted, the legacy external-side-effects mode remains the
fallback. This preserves production behavior while allowing stage to use real
answers safely:

```dotenv
EXTERNAL_SIDE_EFFECTS_MODE=sandbox
EXTERNAL_AI_MODE=enabled
OPENAI_API_KEY=<secret-manager-value>
```

Never put the key in a committed `.env` file or expose it in configuration
screenshots/logs. A missing key must remain a visible, fail-closed readiness
failure rather than silently returning a fake answer.

## Troubleshooting sandbox answers

Check policy propagation before rotating a valid API key:

| Observation | Meaning | Correction |
| --- | --- | --- |
| Key configured, web policy `sandbox` | Web omitted the AI-only override | Pass `EXTERNAL_AI_MODE` to web and maintenance |
| Web `enabled`, maintenance `sandbox` | Search may work but indexing jobs can create fake vectors | Fail deployment parity; make both values identical |
| AI `enabled`, broad effects `sandbox` | Expected stage least-privilege posture | No broad-policy change required |
| AI `enabled`, key missing | Real provider was requested but cannot start | Install the secret; do not fall back silently |
| Stage/review/production AI `sandbox` | Unsafe reachable-environment configuration | Deployment/search fails closed; set reviewed `enabled` or `disabled` |

After deployment, verify the policy without printing credentials, then run one
representative search that requires references. Reject any acceptance response
containing `[SANDBOX]`, an empty answer, or an empty reference set.

Answer cache keys include the environment, effective provider mode, chat
model, release identity, question, context, and reference titles. Provider
policy is validated before a cache read, so a cached development answer cannot
hide a stage/production configuration error.
