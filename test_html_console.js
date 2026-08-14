/**
 * Test HTML file in headless browser and capture console errors
 */

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const HTML_FILE = process.argv[2] || 'test_output/conformance/conformance_test.html';
const ABSOLUTE_PATH = path.resolve(HTML_FILE);

if (!fs.existsSync(ABSOLUTE_PATH)) {
  console.error(`❌ File not found: ${ABSOLUTE_PATH}`);
  process.exit(1);
}

console.log(`\n📄 Opening: ${ABSOLUTE_PATH}`);
console.log(`📊 File size: ${fs.statSync(ABSOLUTE_PATH).size} bytes\n`);

async function runTests() {
  let browser;
  const consoleMessages = {
    log: [],
    warn: [],
    error: [],
    info: [],
    debug: []
  };
  const pageErrors = [];

  try {
    console.log('🚀 Launching headless Chrome...');
    browser = await puppeteer.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage'
      ]
    });

    const page = await browser.newPage();

    // Capture console messages
    page.on('console', msg => {
      const type = msg.type();
      const text = msg.text();
      consoleMessages[type].push(text);

      const prefix = {
        error: '❌ [ERROR]',
        warn: '⚠️  [WARN]',
        info: 'ℹ️  [INFO]',
        log: '📝 [LOG]',
        debug: '🐛 [DEBUG]'
      }[type] || `[${type.toUpperCase()}]`;

      console.log(`${prefix} ${text}`);
    });

    // Capture page errors
    page.on('pageerror', error => {
      const errorStr = error.toString();
      pageErrors.push(errorStr);
      console.error(`❌ [PAGE ERROR] ${errorStr}`);
    });

    // Capture request failures
    page.on('requestfailed', request => {
      const failure = `Request failed: ${request.url()} - ${request.failure().errorText}`;
      consoleMessages.error.push(failure);
      console.error(`❌ [REQUEST FAILED] ${failure}`);
    });

    console.log('📄 Loading HTML file...\n');

    // Navigate to the file
    const response = await page.goto(`file://${ABSOLUTE_PATH}`, {
      waitUntil: 'networkidle0',
      timeout: 30000
    });

    console.log(`\n✓ Page loaded with status: ${response.status()}`);

    // Wait for initial page scripts to execute
    await new Promise(resolve => setTimeout(resolve, 2000));

    console.log('\n📊 Console Messages Summary:');
    console.log(`   Errors:   ${consoleMessages.error.length}`);
    console.log(`   Warnings: ${consoleMessages.warn.length}`);
    console.log(`   Info:     ${consoleMessages.info.length}`);
    console.log(`   Logs:     ${consoleMessages.log.length}`);
    console.log(`   Page Err: ${pageErrors.length}\n`);

    // Check for DuckDB-WASM loading
    const duckdbLoaded = consoleMessages.log.some(msg =>
      msg.toLowerCase().includes('duckdb') ||
      msg.toLowerCase().includes('wasm')
    );
    console.log(`🦆 DuckDB-WASM loading detected: ${duckdbLoaded ? '✅ Yes' : '❓ No explicit messages'}`);

    // Take screenshot for visual verification
    const screenshotPath = '/tmp/test-screenshot.png';
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.log(`📸 Screenshot saved: ${screenshotPath}`);

    // Check if page is actually rendered (not blank)
    const pageTitle = await page.title();
    const bodyText = await page.evaluate(() => document.body.innerText);
    const hasContent = bodyText.length > 100;

    console.log(`\n📄 Page Title: "${pageTitle}"`);
    console.log(`📄 Body content length: ${bodyText.length} chars`);
    console.log(`📄 Page renders content: ${hasContent ? '✅ Yes' : '❌ No (blank or error page)'}`);

    // Auto-click the run-tests button if present
    try {
      const runButtonExists = await page.evaluate(() => {
        const btn = document.getElementById('run-tests');
        return btn && !btn.disabled;
      });

      if (runButtonExists) {
        console.log('\n🎯 Auto-clicking "Run Conformance Tests" button...');
        await page.click('#run-tests');

        // Wait for tests to run
        console.log('⏳ Waiting for tests to complete (10s)...');
        await new Promise(resolve => setTimeout(resolve, 10000));

        // Check final status
        const finalStatus = await page.evaluate(() => {
          const statusDiv = document.getElementById('overall-status');
          return statusDiv ? statusDiv.textContent : 'No status found';
        });
        console.log(`\n📊 Final Test Status: ${finalStatus}`);
      }
    } catch (e) {
      console.error(`⚠️  Could not auto-click button: ${e.message}`);
    }

    // Final screenshot after tests
    const finalScreenshot = '/tmp/test-screenshot-after.png';
    await page.screenshot({ path: finalScreenshot, fullPage: true });
    console.log(`📸 Final screenshot saved: ${finalScreenshot}`);

    // Summary
    console.log('\n' + '='.repeat(60));
    console.log('📋 SUMMARY');
    console.log('='.repeat(60));

    if (consoleMessages.error.length === 0 && pageErrors.length === 0) {
      console.log('✅ SUCCESS: No JavaScript errors detected in console');
    } else {
      console.log('❌ FAILURES FOUND:');
      if (consoleMessages.error.length > 0) {
        console.log(`\n  Console Errors (${consoleMessages.error.length}):`);
        consoleMessages.error.forEach((err, i) => console.log(`    ${i + 1}. ${err}`));
      }
      if (pageErrors.length > 0) {
        console.log(`\n  Page Errors (${pageErrors.length}):`);
        pageErrors.forEach((err, i) => console.log(`    ${i + 1}. ${err}`));
      }
    }

    if (consoleMessages.warn.length > 0) {
      console.log(`\n⚠️  Warnings (${consoleMessages.warn.length}):`);
      consoleMessages.warn.forEach((warn, i) => console.log(`    ${i + 1}. ${warn}`));
    }

    console.log('\n' + '='.repeat(60));

    // Return exit code based on results
    return (consoleMessages.error.length === 0 && pageErrors.length === 0) ? 0 : 1;

  } catch (error) {
    console.error(`\n❌ Fatal error: ${error.message}`);
    console.error(error.stack);
    return 2;
  } finally {
    if (browser) {
      await browser.close();
      console.log('\n✓ Browser closed');
    }
  }
}

runTests()
  .then(exitCode => process.exit(exitCode))
  .catch(error => {
    console.error('Unhandled error:', error);
    process.exit(2);
  });
