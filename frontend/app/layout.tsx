import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

export const metadata = { title: "PENN", description: "Philadelphia street permits and closures" };
export const viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:ital,wght@0,400;0,600;0,700;1,400&family=Barlow+Condensed:wght@500;600;700&display=swap" />
      </head>
      <body>{children}</body>
    </html>
  );
}
