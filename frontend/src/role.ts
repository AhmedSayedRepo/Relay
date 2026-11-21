import { createContext, useContext } from "react";

/** Operator role from the server; the server enforces it, the UI only hides what a user cannot do. */
export type Role = "ADMIN" | "REVIEWER" | "VIEWER";
/** The role plus every page and action the user has (role defaults with the admin's changes). */
export type Access = { role: Role; granted: readonly string[] };

export const RoleContext = createContext<Access>({ role: "ADMIN", granted: [] });

export function useRole(): Access {
  return useContext(RoleContext);
}

export const can = (access: Access, key: string) => access.role === "ADMIN" || access.granted.includes(key);
export const canReview = (access: Access) => can(access, "action.review");
export const isAdmin = (access: Access) => access.role === "ADMIN";
