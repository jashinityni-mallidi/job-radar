# Analyst Openings: setup (about 15 minutes, no coding)

What you get: every hour a free GitHub robot finds new Data Analyst jobs (full-time and contract, last 30 days),
saves them, and emails you when new ones appear. A dashboard page shows them with filters, F-1 status,
apply timing, skills to add to your resume, and a resume ATS checker.

## 1. Get free job-search keys
1. Go to developer.adzuna.com and register (free).
2. Copy your **Application ID** and **Application Key**.

## 2. Create the GitHub project
1. Sign in at github.com. Click **+** > **New repository**. Name it `job-radar`. Choose **Public**. Click **Create repository**.
2. Click **uploading an existing file**. Drag in these 5 files: `fetch_jobs.py`, `build_sponsors.py`, `jobs.json`, `index.html`, `README.md`. Click **Commit changes**.
3. Click **Add file** > **Create new file**. In the name box type exactly: `.github/workflows/fetch-jobs.yml`
   (typing the slashes creates the folders). Paste everything from `workflow-to-paste.yml` into the big box. Click **Commit changes**.

## 3. Add your keys
Repository **Settings** > **Secrets and variables** > **Actions** > **New repository secret**. Add two:
- Name `ADZUNA_APP_ID`, value = your Application ID
- Name `ADZUNA_APP_KEY`, value = your Application Key

Optional: on the **Variables** tab add `JOB_LOCATION` (for example `Dallas, TX`). Leave it out to search the whole US.

## 4. Turn on the dashboard
Settings > **Pages** > Source: **Deploy from a branch** > Branch **main** and folder **/ (root)** > **Save**.
After about a minute your link appears: `https://YOUR-USERNAME.github.io/job-radar/`. Bookmark it.

## 5. Run it the first time
**Actions** tab > **Fetch jobs hourly** > **Run workflow**. (If asked, click "I understand my workflows, go ahead and enable them".)
Wait 1 minute, then refresh your dashboard. After this it runs by itself every hour.

## 6. Email alerts
Each hour with new jobs, the robot opens an issue assigned to you, and GitHub emails you with the list,
when to apply, and the top skills. Check github.com > Settings > Notifications that email is on.

## 7. Turn on the employer H-1B check (one time, then once a quarter)
This uses the US Department of Labor's public H-1B (LCA) disclosure file.
1. Open https://www.dol.gov/agencies/eta/foreign-labor/performance
2. Under **LCA Programs (H-1B, H-1B1, E-3)**, find the newest fiscal year's disclosure data file (Excel).
3. Right-click the file link > **Copy link address**. Do not download it.
4. In your repository: Settings > Secrets and variables > Actions > **Variables** > New repository variable.
   Name `LCA_URL`, value = the link you copied.
5. Run the workflow again (Actions > Fetch jobs hourly > Run workflow). The first run takes a few extra minutes.
Every 3 months DOL posts a newer file. Paste the new link into `LCA_URL` and the list refreshes.

## If something fails
- Red X on a run: open it. "Adzuna rejected the keys" means re-paste the secrets.
- "Permission denied" on push: Settings > Actions > General > Workflow permissions > **Read and write** > Save.
- Dashboard says "Could not load jobs.json": open it from the github.io link, not by double-clicking the file.

## Honest limits
- The hourly check is "about hourly". GitHub can delay scheduled runs by several minutes.
- **H-1B history is a signal, not a promise.** "Has H-1B filings" means the employer filed certified applications with the
  Department of Labor. "No H-1B record" means no match under that company name, which can also happen when a company files
  under a different legal name or has never needed to. Universities and some others may be exempt from the H-1B cap.
- **Citizen/green card detection reads the posting excerpt.** Jobs flagged this way are hidden by default. A wrong flag could
  hide a good job, so untick the box now and then.
- "Says no sponsorship" is shown as a caution, not a rejection. A student on OPT may still be hired, but H-1B later is unlikely.
- Adzuna gives a short excerpt of each posting. F-1 status, skills and recruiter emails are read from that
  excerpt, so many jobs will say "not mentioned". Open the posting to confirm.
- Recruiter names are rarely published. The dashboard shows an email only if the posting contains one, plus a LinkedIn search link.
- "Highly recruiting" means the employer has 3+ open Data Analyst postings, or the posting uses urgent language.
- The ATS score is an estimate. Real systems differ. Your resume is processed in your browser only.
