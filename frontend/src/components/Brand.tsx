import type { ReactNode, SVGProps } from "react";

/** Relay mark "Two sources meet": a receipt line and an SMS line converge on one point. */
export function RelayMark({ size = 36 }: { size?: number }) {
  return (
    <svg className="relay-mark" width={size} height={size} viewBox="0 0 108 108" aria-hidden="true" focusable="false">
      <rect x="4" y="4" width="100" height="100" rx="24" fill="#103E46" />
      <g transform="translate(21 21) scale(.66)" fill="none" strokeWidth="12" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 26h22c10 0 16 6 22 14l8 10" stroke="#F4F6F3" />
        <path d="M14 74h22c10 0 16-6 22-14l8-10" stroke="#EDA06D" />
        <circle cx="78" cy="50" r="12" fill="#35A491" stroke="none" />
      </g>
    </svg>
  );
}

const paths: Record<string, ReactNode> = {
  dashboard: <><rect x="3.5" y="3.5" width="7" height="8" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="5" rx="1.5" /><rect x="13.5" y="11.5" width="7" height="9" rx="1.5" /><rect x="3.5" y="14.5" width="7" height="6" rx="1.5" /></>,
  transactions: <><path d="M4 8h13l-3.5-3.5" /><path d="M20 16H7l3.5 3.5" /></>,
  duplicates: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></>,
  review: <><circle cx="12" cy="12" r="8.5" /><path d="m8.5 12.2 2.4 2.4 4.8-5" /></>,
  processing: <><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" /><path d="M19.5 4.5v4h-4" /></>,
  reports: <><path d="M5 20V10" /><path d="M10 20V4" /><path d="M15 20v-7" /><path d="M20 20V8" /></>,
  devices: <><rect x="6.5" y="2.5" width="11" height="19" rx="2.5" /><path d="M10.5 18.5h3" /></>,
  account: <><circle cx="8.5" cy="12" r="4" /><path d="M12.5 12H21v3" /><path d="M17.5 12v2.5" /></>,
  help: <><circle cx="12" cy="12" r="8.5" /><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.7" /><path d="M12 17h.01" /></>,
  menu: <><path d="M4 7h16" /><path d="M4 12h16" /><path d="M4 17h16" /></>,
  close: <><path d="m6 6 12 12" /><path d="M18 6 6 18" /></>,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" /></>,
  download: <><path d="M12 4v11" /><path d="m7.5 10.5 4.5 4.5 4.5-4.5" /><path d="M5 19.5h14" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  link: <><rect x="6.5" y="2.5" width="11" height="19" rx="2.5" /><path d="M9.5 9.5h5v5h-5z" /></>,
  back: <><path d="M15 5 8 12l7 7" /></>,
  users: <><circle cx="9" cy="8.5" r="3.5" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" /><path d="M16 5.5a3.5 3.5 0 0 1 0 6.5" /><path d="M18 14.3c1.8.8 3 2.6 3 4.7" /></>,
};

export type IconName = keyof typeof paths;

export function Icon({ name, size = 20, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg className={"icon icon-" + name} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}>
      {paths[name]}
    </svg>
  );
}
