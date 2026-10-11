import { useEffect } from "react";

export function useDocumentTitle(title?: string): void {
  useEffect(() => {
    document.title = title ? `${title} | N-ATLAS Forge` : "N-ATLAS Forge";
  }, [title]);
}
