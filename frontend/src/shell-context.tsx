import { createContext, useContext, useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import { useLocation } from "react-router-dom";

export type Heading = {
  path: string;
  title: string;
  section: string;
  parent?: string;
};
export const HeadingContext = createContext<Dispatch<SetStateAction<Heading>>>(
  () => {},
);
export function useHeading(title: string, section: string, parent?: string) {
  const { pathname } = useLocation();
  const set = useContext(HeadingContext);
  useEffect(() => {
    set({ path: pathname, title, section, parent });
  }, [set, pathname, title, section, parent]);
}
