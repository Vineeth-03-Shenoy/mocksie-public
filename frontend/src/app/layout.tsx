import "./globals.css";

export const metadata = { title: "Mocksie", description: "Mock interview practice", robots: "noindex, nofollow" };

export default function Root({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body>{children}</body>
    </html>
  );
}
