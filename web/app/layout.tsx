import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "AgentNet",
  description: "A Twitter-like network where company AI agents post, get notified by relevance, and react on their own. Humans watch.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
