#!/usr/bin/env node

const puppeteer = require('puppeteer');
const http = require('http');

async function testPage() {
    console.log('🚀 Starting headless browser test...\n');

    const browser = await puppeteer.launch({
        headless: 'new',
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const page = await browser.newPage();

    // Capture console messages
    const consoleMessages = [];
    page.on('console', msg => {
        const type = msg.type();
        const text = msg.text();
        consoleMessages.push({ type, text });
        console.log(`[${type.toUpperCase()}] ${text}`);
    });

    // Capture page errors
    const pageErrors = [];
    page.on('pageerror', error => {
        pageErrors.push(error.message);
        console.error('❌ PAGE ERROR:', error.message);
    });

    // Capture all requests
    const allRequests = [];
    page.on('request', request => {
        allRequests.push({ url: request.url(), method: request.method() });
        console.log(`📤 [${request.method()}] ${request.url()}`);
    });

    // Capture request failures
    const failedRequests = [];
    page.on('requestfailed', request => {
        const failure = request.failure();
        failedRequests.push({
            url: request.url(),
            error: failure ? failure.errorText : 'Unknown error'
        });
        console.error('❌ REQUEST FAILED:', request.url(), '-', failure ? failure.errorText : 'Unknown error');
    });

    // Capture response statuses
    const responses = [];
    page.on('response', response => {
        const status = response.status();
        responses.push({ url: response.url(), status });
        if (status >= 400) {
            console.error(`⚠️  RESPONSE ${status}: ${response.url()}`);
        }
    });

    try {
        console.log('📄 Loading page: http://localhost:8765/conformance_test.html');
        await page.goto('http://localhost:8765/conformance_test.html', {
            waitUntil: 'networkidle2',
            timeout: 30000
        });

        console.log('\n✅ Page loaded successfully');

        // Wait a bit for any async operations
        await new Promise(resolve => setTimeout(resolve, 3000));

        // Check if the page rendered content
        const title = await page.title();
        console.log(`📋 Page title: "${title}"`);

        const bodyText = await page.evaluate(() => document.body.textContent);
        if (bodyText.trim().length > 0) {
            console.log('✅ Page rendered visible content');
        } else {
            console.log('❌ Page appears to be blank or empty');
        }

        // Check for specific elements
        const runButton = await page.$('#run-tests');
        if (runButton) {
            console.log('✅ "Run Tests" button found');

            // Click the button to run the conformance tests
            console.log('\n🔄 Clicking "Run Tests" button to execute conformance tests...');
            await runButton.click();

            // Wait for tests to complete (up to 30 seconds)
            console.log('⏳ Waiting for conformance tests to complete...');
            await new Promise(resolve => setTimeout(resolve, 30000));

            // Check the final status
            const finalStatus = await page.evaluate(() => {
                const statusDiv = document.getElementById('overall-status');
                return statusDiv ? statusDiv.textContent : 'No status found';
            });
            console.log(`\n📋 Final Status: ${finalStatus}`);

            // Get test results
            const testResults = await page.evaluate(() => {
                const resultsDiv = document.getElementById('test-results');
                return resultsDiv ? resultsDiv.innerHTML : 'No results';
            });
            console.log('\n📋 Test Results:');
            console.log(testResults.substring(0, 1000));

        } else {
            console.log('❌ "Run Tests" button not found');
        }

        // Summary
        console.log('\n📊 TEST SUMMARY:');
        console.log('='.repeat(50));

        const errors = consoleMessages.filter(m => m.type === 'error');
        const warnings = consoleMessages.filter(m => m.type === 'warning');
        const infoLogs = consoleMessages.filter(m => m.type === 'info' || m.type === 'log');

        console.log(`Console Errors: ${errors.length}`);
        if (errors.length > 0) {
            errors.forEach(e => console.log(`  ❌ ${e.text}`));
        }

        console.log(`Console Warnings: ${warnings.length}`);
        if (warnings.length > 0) {
            warnings.forEach(w => console.log(`  ⚠️  ${w.text}`));
        }

        console.log(`Page Errors: ${pageErrors.length}`);
        if (pageErrors.length > 0) {
            pageErrors.forEach(e => console.log(`  ❌ ${e}`));
        }

        console.log(`Failed Requests: ${failedRequests.length}`);
        if (failedRequests.length > 0) {
            failedRequests.forEach(r => console.log(`  ❌ ${r.url}: ${r.error}`));
        }

        console.log(`Info/Debug Logs: ${infoLogs.length}`);
        if (infoLogs.length > 0) {
            console.log('  📝 Latest logs:');
            infoLogs.slice(-3).forEach(l => console.log(`     ${l.text}`));
        }

        // Overall result
        console.log('\n' + '='.repeat(50));
        if (errors.length === 0 && pageErrors.length === 0 && failedRequests.length === 0) {
            console.log('✅ SUCCESS: No console errors detected!');
            console.log('✅ DuckDB-WASM should load successfully');
        } else {
            console.log('❌ FAILURE: Issues detected (see above)');
        }

    } catch (error) {
        console.error('❌ Test failed:', error.message);
    } finally {
        await browser.close();
    }
}

testPage().catch(console.error);
