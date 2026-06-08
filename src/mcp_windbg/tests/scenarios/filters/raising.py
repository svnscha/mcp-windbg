# Test fixture: a hook that raises. The server wraps the failure as a tool
# error. Used by filter_error.yaml.


def process_output(text, context):
    raise ValueError("boom")
