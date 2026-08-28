import { useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";

const STORAGE_KEY = "theme-pref";

function applyTheme(pref: ThemePref) {
  const root = document.documentElement;
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
}

function readStoredPref(): ThemePref {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

// Applied once at module load (before React renders) so there's no
// flash-of-wrong-theme on boot.
applyTheme(readStoredPref());

export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(readStoredPref);

  useEffect(() => {
    applyTheme(pref);
    localStorage.setItem(STORAGE_KEY, pref);
  }, [pref]);

  const cycle = () => {
    setPref((prev) => (prev === "system" ? "light" : prev === "light" ? "dark" : "system"));
  };

  return { pref, cycle };
}
