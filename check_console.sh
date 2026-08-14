#!/bin/bash
# Script to check HTML file for console errors using Chrome headless

HTML_FILE="$1"
if [ -z "$HTML_FILE" ]; then
    HTML_FILE="test_output/conformance/conformance_test.html"
fi

ABSOLUTE_PATH="$(realpath "$HTML_FILE")"
echo "Opening: $ABSOLUTE_PATH"

# Find Chrome
CHROME=$(which google-chrome-stable google-chrome chromium-browser chromium 2>/dev/null | head -1)
if [ -z "$CHROME" ]; then
    echo "❌ No Chrome browser found"
    exit 1
fi

echo "Using: $CHROME"

# Create temp directory for Chrome
TMP_DIR="/tmp/chrome-check-$$"
mkdir -p "$TMP_DIR"

# Create a script that will capture console output
cat > "$TMP_DIR/console-capture.js" << 'EOF'
// This script will be injected to capture console messages
window.consoleErrors = [];
window.consoleWarnings = [];
window.consoleLogs = [];

const originalError = console.error;
const originalWarn = console.warn;
const originalLog = console.log;

console.error = function(...args) {
    window.consoleErrors.push(args.join(' '));
    originalError.apply(console, args);
};

console.warn = function(...args) {
    window.consoleWarnings.push(args.join(' '));
    originalWarn.apply(console, args);
};

console.log = function(...args) {
    window.consoleLogs.push(args.join(' '));
    originalLog.apply(console, args);
};

window.addEventListener('error', function(e) {
    window.consoleErrors.push(`Uncaught: ${e.message} at ${e.filename}:${e.lineno}`);
});

window.addEventListener('load', function() {
    // Auto-click the run button after a delay
    setTimeout(function() {
        const runButton = document.getElementById('run-tests');
        if (runButton && !runButton.disabled) {
            console.log('Auto-clicking run-tests button...');
            runButton.click();
        }
    }, 1000);
});
EOF

# Create wrapped HTML with console capture
WRAPPED_HTML="$TMP_DIR/wrapped.html"
sed "s/<head>/<head><script src=\"console-capture.js\"><\/script>/" "$ABSOLUTE_PATH" > "$WRAPPED_HTML"

# Start Chrome with remote debugging
echo "Starting Chrome headless..."
"$CHROME" \
    --user-data-dir="$TMP_DIR" \
    --headless=new \
    --no-sandbox \
    --disable-setuid-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --remote-allow-origins=* \
    --remote-debugging-port=9222 \
    "file://$WRAPPED_HTML" \
    > /tmp/chrome-output-$$ 2>&1 &

CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"

# Wait for page to load and tests to run
echo "Waiting for page load and test execution..."
sleep 10

# Try to get console logs via CDP
echo "Attempting to fetch console logs..."
sleep 2

# Check if we can connect to Chrome debugging port
if curl -s http://localhost:9222/json > /dev/null 2>&1; then
    echo "✓ Chrome debugging port accessible"

    # Get page info
    curl -s http://localhost:9222/json/version | jq -r '. // "Unable to get version"' || echo "Failed to get version"

    # Get list of pages
    PAGES=$(curl -s http://localhost:9222/json)
    echo "Pages found:"
    echo "$PAGES" | jq -r '.[].title' 2>/dev/null || echo "Unable to parse page list"
else
    echo "⚠ Chrome debugging port not accessible"
fi

# Check output file
if [ -f "/tmp/chrome-output-$$" ]; then
    echo ""
    echo "=== Chrome Output ==="
    cat "/tmp/chrome-output-$$"
    echo ""
fi

# Kill Chrome
kill -TERM $CHROME_PID 2>/dev/null || true
wait $CHROME_PID 2>/dev/null || true

# Cleanup
rm -rf "$TMP_DIR"
rm -f "/tmp/chrome-output-$$"

echo ""
echo "=== Manual Check Required ==="
echo "Due to Chrome headless limitations, please open the file manually:"
echo "  file://$ABSOLUTE_PATH"
echo ""
echo "And check the browser console (F12) for any errors."
