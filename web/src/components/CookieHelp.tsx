import { useState } from "react";
import { cn } from "@/lib/utils";

/**
 * How to actually get the two cookies.
 *
 * "Open DevTools → Application → Cookies" is only instructions if you already
 * know where those are. Most people do not, so this walks it, names what the
 * values look like so you know when you have the right one, and says plainly
 * where they go and what they can do.
 */
const STEPS: { n: number; title: string; body: React.ReactNode }[] = [
  {
    n: 1,
    title: "Sign in to ESPN in a normal browser tab",
    body: (
      <>Go to <code className="text-chalk">fantasy.espn.com</code> and open your
      league. You have to be logged in — the cookies only exist once you are.</>
    ),
  },
  {
    n: 2,
    title: "Open your browser's developer tools",
    body: (
      <>
        Press <kbd className="kbd">⌥</kbd> <kbd className="kbd">⌘</kbd>{" "}
        <kbd className="kbd">I</kbd> on a Mac, or <kbd className="kbd">F12</kbd>{" "}
        on Windows. A panel opens on the side or bottom of the page.
      </>
    ),
  },
  {
    n: 3,
    title: "Find the cookie list",
    body: (
      <>In that panel, click the <b className="text-chalk">Application</b> tab
      along the top. If you do not see it, it is hidden behind the{" "}
      <b className="text-chalk">»</b> overflow arrow. Then in the left sidebar
      expand <b className="text-chalk">Cookies</b> and click{" "}
      <code className="text-chalk">https://fantasy.espn.com</code>.</>
    ),
  },
  {
    n: 4,
    title: "Copy the two values",
    body: (
      <>
        <span className="block">
          There is a search box above the cookie table — type{" "}
          <code className="text-chalk">swid</code> to filter, then{" "}
          <code className="text-chalk">espn_s2</code>.
        </span>
        <span className="mt-1.5 block">
          <b className="text-chalk">SWID</b> is short and looks like{" "}
          <code className="break-all text-chalk">{"{1A2B3C4D-5E6F-...}"}</code> —
          keep the curly braces.
        </span>
        <span className="mt-1 block">
          <b className="text-chalk">espn_s2</b> is very long (200+ characters) and
          full of <code className="text-chalk">%</code> signs. Double-click the
          Value cell to select all of it before copying — it is easy to grab only
          part.
        </span>
      </>
    ),
  },
  {
    n: 5,
    title: "Paste them below, or put them in .env",
    body: (
      <>Paste into the two fields, or save them once in a file called{" "}
      <code className="text-chalk">.env</code> next to the app as{" "}
      <code className="text-chalk">ESPN_S2=…</code> and{" "}
      <code className="text-chalk">SWID=…</code> and leave the fields blank.
      That file is gitignored.</>
    ),
  },
];

export function CookieHelp() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-line">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted transition-colors hover:text-chalk"
      >
        <span className={cn("inline-block transition-transform duration-200",
          open && "rotate-90")}>›</span>
        Private league? Here is exactly how to get your cookies
      </button>

      <div className={cn("grid transition-[grid-template-rows] duration-300 ease-out",
        open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
        <div className="min-h-0 overflow-hidden">
          <div className="space-y-3 border-t border-line px-3 py-3">
            <p className="text-[11px] leading-relaxed text-muted">
              A <b className="text-chalk">public</b> league needs none of this —
              try connecting first and only come back here if it says ESPN
              refused. Private leagues need two cookies that prove you are a
              member.
            </p>

            <ol className="space-y-2.5">
              {STEPS.map((s) => (
                <li key={s.n} className="flex gap-2.5">
                  <span className="num mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-turf/40 text-[10px] font-bold text-turf">
                    {s.n}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[11.5px] font-medium text-chalk">{s.title}</p>
                    <div className="mt-0.5 text-[11px] leading-relaxed text-muted">
                      {s.body}
                    </div>
                  </div>
                </li>
              ))}
            </ol>

            <p className="rounded border border-line bg-ink px-2.5 py-2 text-[10.5px] leading-relaxed text-muted">
              <b className="text-chalk">What these can do:</b> they are your ESPN
              login session, so treat them like a password — do not paste them
              anywhere else. This app sends them only to espn.com, only to read
              your draft, and never writes anything back. They stay on this
              machine. Logging out of ESPN invalidates them.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
