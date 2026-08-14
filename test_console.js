const puppeteer = require('puppeteer');
const path = require('path');

async function testConsole() {
    console.log('Launching browser...');
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
        console.log(`Browser console [${type}]:`, text);
    });

    // Capture page errors
    page.on('pageerror', error => {
        console.log('Page error:', error.message);
        consoleMessages.push({ type: 'error', text: error.message });
    });

    // Capture failed requests
    page.on('requestfailed', request => {
        console.log('Request failed:', request.url(), request.failure().errorText);
        consoleMessages.push({ type: 'requestfailed', text: `${request.url()} - ${request.failure().errorText}` });
    });

    try {
        const filePath = 'file://' + path.resolve(__dirname, 'web/index.html');
        console.log('Loading file:', filePath);

        await page.goto(filePath, { waitUntil: 'networkidle0', timeout: 30000 });

        // Wait a bit for any async operations
        await new Promise(resolve => setTimeout(resolve, 3000));

        console.log('\n=== Page Title ===');
        const title = await page.title();
        console.log(title);

        console.log('\n=== Checking for visible content ===');
        const visibleContent = await page.evaluate(() => {
            const body = document.body;
            return {
                hasContent: body.innerText.length > 100,
                innerHTMLLength: body.innerHTML.length,
                innerTextLength: body.innerText.length,
                buttonCount: document.querySelectorAll('button').length,
                testSections: document.querySelectorAll('.test-section').length
            };
        });
        console.log('Visible content check:', visibleContent);

        console.log('\n=== Summary ===');
        console.log(`Total console messages: ${consoleMessages.length}`);
        const errors = consoleMessages.filter(m => m.type === 'error');
        const warnings = consoleMessages.filter(m => m.type === 'warning');
        console.log(`Errors: ${errors.length}`);
        console.log(`Warnings: ${warnings.length}`);

        if (errors.length > 0) {
            console.log('\n=== ERRORS FOUND ===');
            errors.forEach(e => console.log('  -', e.text));
            process.exit(1);
        } else {
            console.log('\n✅ No JavaScript errors found!');
            process.exit(0);
        }

    } catch (error) {
        console.error('Error during test:', error);
        process.exit(1);
    } finally {
        await browser.close();
    }
}

testConsole().catch(error => {
    console.error('Fatal error:', error);
    process.exit(1);
});
