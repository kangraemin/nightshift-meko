"""Create a fresh datapack for one benchmark seed and share it with shift B (Contributor).

Meko has no MCP tool for sharing, so this drives cloud.mekodata.ai in headless Chromium
with each member's email + password. Promoted memories cannot be deleted over MCP, so a
fresh datapack per seed is how every run starts with an empty team memory.

    python scripts/share_datapack.py nightshift-s2        # prints the datapack id
Env: MEKO_API_KEY, MEKO_EMAIL, MEKO_PASSWORD, MEKO_B_EMAIL, MEKO_B_PASSWORD
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nightshift.meko import Meko  # noqa: E402

PORTAL = "https://cloud.mekodata.ai"


def login(browser, email, password):
    pg = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
    pg.goto(f"{PORTAL}/signin")
    pg.fill("input[name=email]", email)
    pg.fill("input[name=password]", password)
    pg.click("button:has-text('Continue') >> nth=0")
    pg.wait_for_timeout(5000)
    pg.goto(f"{PORTAL}/datapacks")
    pg.wait_for_timeout(6000)
    return pg


def mouse_click(pg, locator):
    locator.wait_for(state="visible", timeout=15000)
    box = locator.bounding_box()
    pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pg.wait_for_timeout(300)
    pg.mouse.down()
    pg.mouse.up()


def find_or_create(name):
    a = Meko("A", os.environ["MEKO_API_KEY"], os.environ["NIGHTSHIFT_DATAPACK_ID"])
    cid = a.conversation("nightshift:admin", f"datapack for {name}")
    listing = a.call("datapack_list", limit=50, conversation_id=cid)
    rows = listing if isinstance(listing, list) else (listing.get("datapacks") or listing.get("results") or [])
    for r in rows:
        if name in (r.get("datapack_name"), r.get("name")):
            return r.get("datapack_id") or r.get("id")
    r = a.call("datapack_create", name=name, conversation_id=cid)
    if "datapack_id" not in r:
        raise SystemExit(f"datapack_create failed: {r}")
    return r["datapack_id"]


def invite(pg, name, email):
    names = pg.evaluate("""()=>[...document.querySelectorAll('button[title="Share this datapack"]')].map(b=>{
        let c=b; while(c && !c.innerText.includes('Access')) c=c.parentElement; return c ? c.innerText.split('\\n')[0] : ''})""")
    i = names.index(name)
    btn = pg.locator('button[title="Share this datapack"]').nth(i)
    btn.scroll_into_view_if_needed()
    mouse_click(pg, btn)
    pg.wait_for_timeout(2500)
    body = pg.inner_text("body")
    if email.lower() in body.lower():
        return "already member or pending"
    box = pg.locator("textarea").first
    box.fill(email)
    mouse_click(pg, pg.locator("button", has_text="Contributor").first)
    pg.wait_for_timeout(500)
    mouse_click(pg, pg.locator("button", has_text="Send invite").first)
    pg.wait_for_timeout(4000)
    return "invited" if email.lower() in pg.inner_text("body").lower() else "invite not visible"


def accept(pg, name):
    mouse_click(pg, pg.locator('button[aria-label="Datapack share invitations"]:visible').first)
    pg.wait_for_timeout(2500)
    acc = pg.locator("button", has_text="Accept")
    n = acc.count()
    for _ in range(n):
        mouse_click(pg, pg.locator("button", has_text="Accept").first)
        pg.wait_for_timeout(3000)
    pg.goto(f"{PORTAL}/datapacks")
    pg.wait_for_timeout(6000)
    return n, name in pg.inner_text("body")


def main():
    name = sys.argv[1]
    dp = find_or_create(name)
    with sync_playwright() as p:
        br = p.chromium.launch()
        a = login(br, os.environ["MEKO_EMAIL"], os.environ["MEKO_PASSWORD"])
        print("A:", invite(a, name, os.environ["MEKO_B_EMAIL"]))
        b = login(br, os.environ["MEKO_B_EMAIL"], os.environ["MEKO_B_PASSWORD"])
        n, ok = accept(b, name)
        print(f"B: accepted {n} invite(s); sees {name}: {ok}")
        br.close()
    print(dp)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
