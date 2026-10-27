import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "Admin Console — WeRU.B",
  description: "HR Law + Game NPC Admin",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen flex">
        <Sidebar />
        <main className="ml-60 flex-1 p-8">{children}</main>
      </body>
    </html>
  );
}
