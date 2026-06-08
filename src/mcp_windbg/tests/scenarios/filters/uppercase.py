# Test fixture: a single-argument process_output hook (no context parameter).
# Used by filter_redaction.yaml to prove the --filter-script plumbing rewrites
# tool output through the real hosted server.


def process_output(text):
    return text.upper()
