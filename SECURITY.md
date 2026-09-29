# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.0.x   | Yes       |
| 1.0.x   | No        |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

**Do NOT open a public issue.**

Instead, please report it by:

1. Opening a [private security advisory](https://github.com/Barlows/ha-addon-km140f/security/advisories/new)
2. Or contacting the maintainer directly

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and aim to provide a fix or mitigation within 7 days.

## Security Considerations

- MQTT credentials are passed via environment variables (not hardcoded)
- TLS support is available for MQTT connections
- No external network calls beyond the configured MQTT broker and KM140F device
