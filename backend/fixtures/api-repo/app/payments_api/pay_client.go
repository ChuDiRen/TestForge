// Package paymentsapi 支付渠道客户端（多语言索引演示：Go）。
package paymentsapi

import "errors"

// ErrChannelDown 渠道不可用。
var ErrChannelDown = errors.New("CHANNEL_DOWN")

// ChargeWithChannel 走指定渠道扣款，金额必须为正。
func ChargeWithChannel(channel string, orderID string, cents int64) (string, error) {
	if cents <= 0 {
		return "", errors.New("AMOUNT_INVALID")
	}
	if channel == "" {
		return "", ErrChannelDown
	}
	return "pay-" + orderID, nil
}

// refundChannel 返回渠道退款端点名（内部方法）。
func (c *Client) refundChannel() string {
	return c.name + "/refund"
}
