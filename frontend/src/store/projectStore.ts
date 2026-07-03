import { create } from 'zustand'
import type { Project } from '../types/api'

interface ProjectStore {
  currentProject: Project | null
  projects: Project[]
  setCurrentProject: (p: Project | null) => void
  setProjects: (ps: Project[]) => void
}

export const useProjectStore = create<ProjectStore>((set) => ({
  currentProject: null,
  projects: [],
  setCurrentProject: (p) => set({ currentProject: p }),
  setProjects: (ps) => set({ projects: ps }),
}))
