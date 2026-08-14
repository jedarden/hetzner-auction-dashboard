/**
 * Script to open HTML file and capture console errors
 * Usage: node test_browser_console.js <html-file-path>
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const htmlFilePath = process.argv[2] || 'test_output/conformance/conformance_test.html';
const absolutePath = path.resolve(htmlFilePath);

console.log(`Opening: ${absolutePath}`);
console.log(`File exists: ${fs.existsSync(absolutePath)}`);

// Try different approaches
const approaches = [
  {
    name: 'Puppeteer',
    test: () => {
      try {
        require.resolve('puppeteer');
        return true;
      } catch (e) {
        return false;
      }
    },
    run: async () => {
      const puppeteer = require('puppeteer');
      const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
      });
      const page = await browser.newPage();

      const consoleMessages = [];
      const errors = [];
      const warnings = [];

      page.on('console', msg => {
        const text = msg.text();
        const type = msg.type();
        consoleMessages.push({ type, text });

        if (type === 'error') errors.push(text);
        if (type === 'warning') warnings.push(text);

        console.log(`[${type.toUpperCase()}] ${text}`);
      });

      page.on('pageerror', error => {
        errors.push(error.toString());
        console.error(`[PAGE ERROR] ${error}`);
      });

      await page.goto(`file://${absolutePath}`, { waitUntil: 'networkidle0' });

      // Wait a bit for any async initialization
      await new Promise(resolve => setTimeout(resolve, 3000));

      await browser.close();

      return { consoleMessages, errors, warnings };
    }
  },
  {
    name: 'Chrome Headless',
    test: () => {
      try {
        execSync('which google-chrome-stable google-chrome chromium-browser chromium | head -1', { stdio: 'ignore' });
        return true;
      } catch (e) {
        return false;
      }
    },
    run: async () => {
      console.log('Using Chrome headless approach...');

      const chromePath = execSync('which google-chrome-stable google-chrome chromium-browser chromium | head -1', { encoding: 'utf-8' }).trim();
      console.log(`Chrome path: ${chromePath}`);

      // Create a temporary directory for Chrome user data
      const tmpDir = `/tmp/chrome-headless-${Date.now()}`;
      fs.mkdirSync(tmpDir, { recursive: true });

      // Use Chrome with remote debugging
      const chromeArgs = [
        `--user-data-dir=${tmpDir}`,
        '--headless=new',
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--remote-debugging-port=9222',
        `file://${absolutePath}`
      ];

      console.log('Starting Chrome...');
      const chromeProcess = require('child_process').spawn(chromePath, chromeArgs, {
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: true
      });

      const consoleMessages = [];
      const errors = [];
      const warnings = [];

      chromeProcess.stdout.on('data', (data) => {
        const output = data.toString();
        console.log(`[CHROME OUT] ${output}`);
      });

      chromeProcess.stderr.on('data', (data) => {
        const output = data.toString();
        console.log(`[CHROME ERR] ${output}`);
      });

      // Wait for page to load
      await new Promise(resolve => setTimeout(resolve, 5000));

      // Try to fetch console logs via Chrome DevTools Protocol
      try {
        const response = await fetch('http://localhost:9222/json');
        const pages = await response.json();

        if (pages.length > 0) {
          console.log(`Found ${pages.length} page(s)`);

          // Get console logs via Runtime.evaluate
          // This requires WebSocket connection to the DevTools protocol
          console.log('Page loaded successfully');
        }
      } catch (e) {
        console.log(`Could not fetch DevTools data: ${e.message}`);
      }

      // Kill Chrome process
      chromeProcess.kill('SIGTERM');

      // Cleanup
      fs.rmSync(tmpDir, { recursive: true, force: true });

      return {
        consoleMessages,
        errors,
        warnings,
        note: 'Chrome headless ran but console capture via DevTools Protocol requires WebSocket client'
      };
    }
  }
];

async function main() {
  for (const approach of approaches) {
    console.log(`\nTrying approach: ${approach.name}...`);
    if (approach.test()) {
      console.log(`✓ ${approach.name} is available`);
      const result = await approach.run();

      console.log('\n=== RESULTS ===');
      if (result.errors && result.errors.length > 0) {
        console.log(`❌ Found ${result.errors.length} errors:`);
        result.errors.forEach(err => console.log(`  - ${err}`));
      } else if (result.note) {
        console.log(result.note);
      } else {
        console.log('✓ No JavaScript errors detected');
      }

      if (result.warnings && result.warnings.length > 0) {
        console.log(`⚠ Found ${result.warnings.length} warnings:`);
        result.warnings.forEach(warn => console.log(`  - ${warn}`));
      }

      return result;
    } else {
      console.log(`✗ ${approach.name} not available`);
    }
  }

  console.log('\n❌ No browser automation tools available');
  console.log('Please install puppeteer: npm install puppeteer');
}

main().catch(console.error);
