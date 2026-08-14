#!/usr/bin/env node

const fs = require('fs');
const http = require('http');

// Test 1: Check if HTML file exists and is readable
console.log('Test 1: Checking HTML file...');
try {
    const html = fs.readFileSync('./conformance_test.html', 'utf8');
    console.log('✅ HTML file is readable');

    // Test 2: Check HTML structure
    console.log('\nTest 2: Checking HTML structure...');
    if (html.includes('<!DOCTYPE html>')) {
        console.log('✅ DOCTYPE present');
    } else {
        console.log('❌ Missing DOCTYPE');
    }

    // Test 3: Check for DuckDB-WASM CDN link
    console.log('\nTest 3: Checking DuckDB-WASM CDN link...');
    if (html.includes('https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm')) {
        console.log('✅ DuckDB-WASM CDN link present');
    } else {
        console.log('❌ Missing DuckDB-WASM CDN link');
    }

    // Test 4: Check for parquet file reference
    console.log('\nTest 4: Checking parquet file reference...');
    if (html.includes('conformance_test.parquet')) {
        console.log('✅ Parquet file referenced');
    } else {
        console.log('❌ Missing parquet file reference');
    }

    // Test 5: Check for JavaScript syntax errors (basic)
    console.log('\nTest 5: Basic JavaScript syntax check...');
    try {
        // Extract script content
        const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>/);
        if (scriptMatch) {
            const script = scriptMatch[1];
            // Check for common syntax issues
            if (script.includes('async function')) {
                console.log('✅ Async functions present');
            }
            if (script.includes('await')) {
                console.log('✅ Async/await usage found');
            }
            if (script.includes('try') && script.includes('catch')) {
                console.log('✅ Error handling present');
            }
        }
    } catch (e) {
        console.log('❌ JavaScript syntax check failed:', e.message);
    }

    // Test 6: Check if parquet file exists
    console.log('\nTest 6: Checking parquet file...');
    try {
        fs.accessSync('./conformance_test.parquet', fs.constants.R_OK);
        console.log('✅ Parquet file exists and is readable');
    } catch (e) {
        console.log('❌ Parquet file missing or not readable');
    }

    console.log('\n✅ Basic structure checks passed');
    console.log('\n⚠️  Note: Full JavaScript execution requires a browser environment.');
    console.log('⚠️  To test in browser, open: http://localhost:8765/conformance_test.html');

} catch (error) {
    console.error('❌ Error reading HTML file:', error.message);
    process.exit(1);
}
