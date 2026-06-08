# Test fixture: a malformed filter script with neither process_input nor
# process_output. load_filter_script rejects it, so the hosted server should
# fail to start. Used by filter_invalid.yaml (negative_launch).

VALUE = 1
