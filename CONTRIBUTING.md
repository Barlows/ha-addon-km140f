# Contributing to ha-addon-km140f

Thank you for your interest in contributing! This document provides guidelines for contributing to this project.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/ha-addon-km140f.git`
3. Create a branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Run tests: `make test`
6. Run linting: `make lint`
7. Run type checking: `make typecheck`
8. Commit and push
9. Open a Pull Request

## Development Setup

```bash
# Install dependencies
make install

# Run all checks
make all
```

## Code Style

- Python 3.12+
- Type hints required on all functions
- Follow PEP 8 (enforced by ruff)
- All functions must pass mypy type checking

## Testing

- All new features must include tests
- Tests are in the `tests/` directory
- Run tests with `pytest tests/ -v`

## Pull Requests

- Keep PRs focused on a single feature or fix
- Describe what changed and why
- Reference any related issues
- Ensure CI passes before requesting review

## Questions?

Open an issue for discussion before starting significant work.
