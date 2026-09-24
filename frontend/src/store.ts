import { create } from "zustand";

/** 全局联动状态：待审计数 + 跨视图刷新信号 */
interface GlobalState {
  pendingReviews: number;
  bumpRevision: () => void;
  revision: number;
  setPendingReviews: (n: number) => void;
}

export const useGlobal = create<GlobalState>((set) => ({
  pendingReviews: 0,
  revision: 0,
  bumpRevision: () => set((s) => ({ revision: s.revision + 1 })),
  setPendingReviews: (n) => set({ pendingReviews: n }),
}));
