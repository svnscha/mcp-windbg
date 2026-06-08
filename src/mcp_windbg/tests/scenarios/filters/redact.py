# Test fixture: dual-argument hooks (text, context) that rewrite both the
# incoming tool arguments and the outgoing tool text. Used by
# filter_redact_io.yaml to exercise process_input recursion and process_output.


def process_input(text, context):
    return text.replace("secret", "[redacted]")


def process_output(text, context):
    return text.replace("crash dump file", "CRASH_DUMP_FILE")
