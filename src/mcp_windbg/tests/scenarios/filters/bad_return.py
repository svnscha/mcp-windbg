# Test fixture: a hook that returns a non-string. The server reports the
# resulting TypeError as a tool error. Used by filter_bad_return.yaml.


def process_output(text, context):
    return 123
