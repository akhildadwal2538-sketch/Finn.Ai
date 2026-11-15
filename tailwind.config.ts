import type { Config } from 'tailwindcss';
export default { content: ['./app/**/*.{js,ts,jsx,tsx}'], theme: { extend: { colors: { ink: '#0b0f12', panel: '#11171b', line: '#202a2f', muted: '#839096', mint: '#b9f66a', coral: '#ff836f' }, fontFamily: { sans: ['Arial', 'sans-serif'] } } }, plugins: [] } satisfies Config;
