import { Result, Button } from 'antd'

export default function Unauthorized() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
      <Result
        status="403"
        title="未授权访问"
        subTitle="请通过已配置的外部 SSO 或本地登录页进入平台。"
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回
          </Button>
        }
      />
    </div>
  )
}
