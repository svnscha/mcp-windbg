# Test fixture: both hooks return None, meaning "leave unchanged". Used by
# filter_passthrough.yaml to exercise the None-return path.


def process_input(text, context):
    return None


def process_output(text, context):
    return None
