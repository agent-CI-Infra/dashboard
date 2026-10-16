import {
  Activity,
  CircleDot,
  GitPullRequest,
  Globe,
  Info,
  LayoutDashboard,
  LineChart,
  ShieldCheck,
} from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  teams: [
    {
      name: 'AI应用与软件创新Lab',
      logo: Activity,
      plan: '开源社区运营与健康度监控平台',
    },
  ],
  navGroups: [
    {
      title: '',
      items: [
        {
          title: '社区概览',
          url: '/',
          icon: LayoutDashboard,
        },
        {
          title: 'Pull Requests',
          url: '/pulls',
          icon: GitPullRequest,
        },
        {
          title: 'Issues',
          url: '/issues',
          icon: CircleDot,
        },
        {
          title: '社区成长',
          url: '/growth',
          icon: LineChart,
        },
        {
          title: '生态影响力',
          url: '/ecosystem',
          icon: Globe,
        },
        {
          title: '关于本站',
          url: '/about',
          icon: Info,
        },
        {
          title: 'CI GATE',
          url: '/gate',
          icon: ShieldCheck,
        },
      ],
    },
  ],
}