# Portfolio Monitor (installable web app, no server)

Your holdings stay on your phone. GitHub stores the app files for free and a scheduled job
refreshes market data into `market.json`. To change the app later, replace a file on
GitHub. The installed app updates itself the next time you open it. No reinstall.

## Setup (about 15 minutes, no coding)

1. Create a free account at github.com. Create a **new repository** named `portfolio-monitor` and set it to **Public**.
2. Unzip this folder. Open it, press Ctrl+A to select everything, and drag it all into the repository page in your browser
   (**Add file > Upload files**), then Commit. In File Explorer turn on View > Show > Hidden items so the `.github` folder is included.
   If `.github` does not upload, use **Add file > Create new file**, type the name `.github/workflows/update-data.yml`
   (typing the slashes makes the folders), paste the contents of that file, then Commit.
3. **Settings > Actions > General > Workflow permissions**: choose **Read and write permissions**, then Save.
4. **Settings > Pages**: Source = **Deploy from a branch**, Branch = **main**, folder = **/ (root)**, then Save.
   After a minute your app address appears: `https://YOUR-NAME.github.io/portfolio-monitor/`
5. Open `watchlist.json` (pencil icon) and list what you want tracked.
   - Stocks: NSE symbols, for example `"RELIANCE"` (no `.NS`).
   - Funds: AMFI scheme codes. Find one at `https://api.mfapi.in/mf/search?q=parag parikh flexi`
     and check that the name matches the fund you mean. The two codes in the file are examples, so verify them.
6. **Actions** tab > **Update market data** > **Run workflow**. Wait for the green tick (1 to 3 minutes).
   If it turns red, open the run and send me the error text.
7. On your Android phone open the app address in Chrome, then menu (three dots) > **Install app**.
8. In the app tap **Edit** and add what you own: shares or units and the total amount paid.

## How it stays updated
- Every 15 minutes on weekdays during market hours: prices, charts and scores (quick refresh).
- 6 pm IST on weekdays: everything, including fundamentals, analyst votes, news, corporate actions and delivery data.
- Any time you edit `watchlist.json` or `funds_meta.json`.

## Changing the app later
Open `index.html` on GitHub, tap the pencil, paste the new version and Commit. Phones pick it up on the next open.

## Extra fund details (optional)
Free feeds give NAV, returns, category and news for funds. AUM, expense ratio, exit load, fund manager,
holdings, cap and sector split are not available for free. Copy the layout of `funds_meta.example.json`
into `funds_meta.json` (key = scheme code, holdings use NSE symbols) and fill in values from the fund factsheet.

## Honest limits
- Not tested against the live feeds yet. The first runs may need small fixes.
- Data is unofficial and free (Yahoo Finance, AMFI, mfapi.in, Google News, NSE). Prices can be 15 minutes
  or more behind. GitHub may start scheduled runs late. If a source blocks or fails, the last good value is kept
  and the item shows "old data".
- Analyst votes come from Yahoo and cover fewer Indian stocks. Missing factors are left out of the score.
- News tone is a keyword count, not real analysis. Cap size is approximate.
- GitHub pauses scheduled jobs in a public repository after 60 days with no repository activity. Run the workflow
  once by hand to restart it.
- The repository is public. Never put your holdings or personal details in it.
- Clearing browser data deletes your holdings. Use the backup box under Edit.
- Suggestions are a score from free data, not advice. If you ever share them with other people, SEBI rules on
  research analysts and investment advisers may apply.
