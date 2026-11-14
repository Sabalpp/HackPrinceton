import mockJson from "@/mock/state.json";
import type { FarCareState } from "./farcare-state";

/**
 * mock/state.json, type-checked against the proposed contract at build time.
 * Plain module (no "use client"), so server code like generateStaticParams can read it too.
 */
export const MOCK_STATE: FarCareState = mockJson as FarCareState;
