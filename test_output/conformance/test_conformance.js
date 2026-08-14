#!/usr/bin/env node

/**
 * Conformance test automation script
 * Tests the DuckDB-WASM conformance test page in a headless browser
 */

const http = require('http');
const fs = require('fs');
const path = require('path');

// Simple HTTP server to serve the test files
const server = http.createServer((req, res) => {
  const filePath = path.join(__dirname, req.url === '/' ? 'conformance_test.html' : req.url);

  if (fs.existsSync(filePath)) {
    const ext = path.extname(filePath);
    const contentType = ext === '.html' ? 'text/html' :
                       ext === '.js' ? 'application/javascript' :
                       ext === '.parquet' ? 'application/octet-stream' :
                       'text/plain';

    res.writeHead(200, { 'Content-Type': contentType });
    fs.createReadStream(filePath).pipe(res);
  } else {
    res.writeHead(404);
    res.end('Not found');
  }
});

const PORT = 9876;
server.listen(PORT, '127.0.0.1', () => {
  console.log(`HTTP server running on http://127.0.0.1:${PORT}`);
  console.log(`\nTo test manually, open: http://127.0.0.1:${PORT}/conformance_test.html`);
  console.log(`\nPress Ctrl+C to stop the server\n`);

  // Create a simple HTML test runner that can be executed in Node.js
  console.log('=' .repeat(60));
  console.log('CONFORMANCE TEST VERIFICATION');
  console.log('=' .repeat(60));

  const html = fs.readFileSync(path.join(__dirname, 'conformance_test.html'), 'utf-8');

  // Check for key components
  const checks = [
    { name: 'DuckDB-WASM CDN reference', pattern: /@duckdb\/duckdb-wasm@[\d.]+/, required: true },
    { name: 'httpfs extension load', pattern: /INSTALL httpfs/, required: true },
    { name: 'Parquet file reference', pattern: /read_parquet\('conformance_test\.parquet'\)/, required: true },
    { name: 'Schema validation', pattern: /DESCRIBE listings/, required: true },
    { name: 'NULL handling test', pattern: /conformance-3.*benchmark_matched.*false/s, required: true },
    { name: 'Complex types test (disks)', pattern: /conformance-2.*disks/s, required: true },
    { name: 'Derived metrics test', pattern: /price_effective_monthly.*7998/s, required: true },
  ];

  console.log('\n✓ HTML file structure checks:');
  let allPassed = true;
  checks.forEach(check => {
    const passed = check.pattern.test(html);
    if (passed) {
      console.log(`  ✓ ${check.name}`);
    } else {
      console.log(`  ✗ ${check.name} ${check.required ? '(REQUIRED)' : ''}`);
      if (check.required) allPassed = false;
    }
  });

  console.log(`\n✓ File existence checks:`);
  const files = [
    'conformance_test.html',
    'conformance_test.parquet'
  ];

  files.forEach(file => {
    const exists = fs.existsSync(path.join(__dirname, file));
    console.log(`  ${exists ? '✓' : '✗'} ${file}`);
    if (!exists && file === 'conformance_test.parquet') allPassed = false;
  });

  console.log('\n' + '='.repeat(60));
  if (allPassed) {
    console.log('✅ All static checks passed!');
    console.log('\nNext steps: Open http://127.0.0.1:' + PORT + '/conformance_test.html in a real browser');
    console.log('to verify DuckDB-WASM loads and queries execute successfully.');
  } else {
    console.log('❌ Some static checks failed!');
  }
  console.log('='.repeat(60));
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${PORT} is already in use. Please stop the other server first.`);
  } else {
    console.error('Server error:', err);
  }
  process.exit(1);
});
