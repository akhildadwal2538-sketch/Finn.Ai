import './globals.css';
import type { Metadata } from 'next';
export const metadata:Metadata={title:'FINN | Financial Intelligence',description:'The signal behind your portfolio.'};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
