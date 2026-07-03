import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import 'antd/dist/reset.css'
import './styles/index.css'

// 图标字体就绪后再显示 .ms 图标，避免连字名(home/search…)以英文文本闪现；
// 兜底：最多隐藏 3s，且字体加载失败也照常显示，绝不把图标永久藏起来
const revealIcons = () => document.documentElement.classList.add('ms-ready')
if ((document as any).fonts?.load) {
  ;(document as any).fonts.load("24px 'Material Symbols Outlined'").then(revealIcons).catch(revealIcons)
  setTimeout(revealIcons, 3000)
} else {
  revealIcons()
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
