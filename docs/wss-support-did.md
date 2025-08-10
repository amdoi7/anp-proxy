# wss support did

## 背景

wss基于http，http支持did-wba的身份认证，现在需要让wss支持did-wba的身份认证

did wba是使用http头进行处理的，现在需要让wss支持did-wba的身份认证

方法参考 [anp-did-spec.md](./anp-did-spec.md)

## 修改点

修改receiver和gateway之间的wss连接，添加did-wba的身份认证

在发送wss的时候参考[anp-did-spec.md](./anp-did-spec.md)中的方法，添加认证信息，在server端对其进行验证。

