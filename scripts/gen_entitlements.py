"""Generate a macOS entitlements plist for debugger access.

Usage:
    python gen_entitlements.py [output_path]

If output_path is omitted, prints to stdout.
"""

import sys

ENTITLEMENTS = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>com.apple.security.automation.apple-events</key>
	<true/>
	<key>com.apple.security.cs.allow-dyld-environment-variables</key>
	<true/>
	<key>com.apple.security.cs.allow-jit</key>
	<true/>
	<key>com.apple.security.cs.debugger</key>
	<true/>
	<key>com.apple.security.cs.disable-executable-page-protection</key>
	<true/>
	<key>com.apple.security.cs.disable-library-validation</key>
	<true/>
	<key>com.apple.security.get-task-allow</key>
	<true/>
</dict>
</plist>
"""


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            f.write(ENTITLEMENTS)
    else:
        sys.stdout.write(ENTITLEMENTS)


if __name__ == "__main__":
    main()
