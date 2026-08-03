# AI provider policy

Search and chat calls are guarded independently from email, webhooks, and
other external side effects.

`EXTERNAL_AI_MODE` is optional and takes precedence over
`EXTERNAL_SIDE_EFFECTS_MODE` for calls made through `core.ai_guard`:

- `enabled` uses the configured OpenAI provider and requires
  `OPENAI_API_KEY` at call time.
- `sandbox` returns deterministic responses for test/review environments.
- `disabled` fails closed with `ExternalAIBlocked`.

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

After deployment, verify the policy without printing credentials, then run one
representative search that requires references. Reject any acceptance response
containing `[SANDBOX]`, an empty answer, or an empty reference set.
