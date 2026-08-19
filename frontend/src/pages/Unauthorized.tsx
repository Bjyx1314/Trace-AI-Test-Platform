import { Result, Button } from 'antd'

export default function Unauthorized() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
      <Result
        status="403"
        title="未授权访问"
        subTitle="请通过 external task system 进入测试平台，或联系管理员获取权限。"
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回
          </Button>
        }
      />
    </div>
  )
}
