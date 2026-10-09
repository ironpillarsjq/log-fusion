package com.inspection.logbackend.scheduler;

import com.inspection.logbackend.controller.HeartbeatController;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.util.Iterator;
import java.util.Map;

@Component
@Slf4j
public class HeartbeatWatchdog {

    // 判定死亡的阈值：由于 Fluent Bit 5 秒发一次，我们设定 15 秒没收到就认为它挂了
    // 允许容忍 2 次网络抖动造成的心跳丢失
    private static final long TIMEOUT_MS = 15 * 1000;

    @Scheduled(fixedDelay = 3 * 1000) // 每 5 秒巡检一次全局打卡表
    public void inspectClients() {
        long now = System.currentTimeMillis();

        // 使用迭代器，边遍历边安全地删除超时节点
        Iterator<Map.Entry<String, Long>> iterator = HeartbeatController.clientLastSeenMap.entrySet().iterator();

        while (iterator.hasNext()) {
            Map.Entry<String, Long> entry = iterator.next();
            String clientId = entry.getKey();
            Long lastSeen = entry.getValue();

            // 如果 当前时间 - 上次打卡时间 > 15秒
            if (now - lastSeen > TIMEOUT_MS) {

                // 1. ⚠️ 触发你的一定操作
                executeFailureAction(clientId);

                // 2. 移出队列，防止下个5秒巡检时重复触发相同告警
                iterator.remove();
            }
        }
    }

    /**
     * 心跳异常时，后端需要执行的相应操作
     */
    private void executeFailureAction(String clientId) {
        log.warn("【核心警告】检测到客户端机器 [{}] 失去连接，Fluent Bit 进程可能已崩溃或网络断开！", clientId);
        // ============================================================
        // TODO: 在这里编写你后端需要做的具体“一定操作”，例如：
        //
        // 选项 A：发送告警（调用你的邮件服务、或者钉钉/企业微信机器人 Webhook）
        // sendAlertToDingTalk(clientId);
        //
        // 选项 B：修改数据库中该客户端的状态，让前端大屏显示红色的“离线”
        // clientDeviceRepository.updateStatusByClientId(clientId, "OFFLINE");
        //
        // 选项 C：释放资源或执行某些解绑逻辑
        // ============================================================
    }
}