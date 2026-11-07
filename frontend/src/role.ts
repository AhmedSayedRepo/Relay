import { createContext, useContext } from "react";

/** Operator role from the server; the server enforces it, the UI only hides what a role cannot do. */
export type Role = "ADMIN" | "REVIEWER" | "VIEWER";

export const RoleContext = createContext<Role>("ADMIN");

export function useRole(): Role {
  return useContext(RoleContext);
}

export const canReview = (role: Role) => role !== "VIEWER";
export const isAdmin = (role: Role) => role === "ADMIN";
