# Test fixture: only process_input is defined, so process_output leaves output
# untouched. Used by filter_input_only.yaml.


def process_input(text, context):
    return text
