<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import type { TrendPoint } from '@/types/monitor'
import { formatHourMinute } from '@/utils/format'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = withDefaults(
  defineProps<{
    points: TrendPoint[]
    /** 趋势时间范围（小时） */
    hours?: number
    hoursOptions?: number[]
  }>(),
  { hours: 24, hoursOptions: () => [6, 12, 24] },
)

const emit = defineEmits<{ (e: 'update:hours', hours: number): void }>()

const chartEl = ref<HTMLDivElement | null>(null)
/** 图表实例只创建一次，数据变化只调用 setOption，避免每 5 秒重建实例造成内存泄漏 */
let chart: ReturnType<typeof echarts.init> | undefined
let observer: ResizeObserver | undefined

function buildOption(points: TrendPoint[]): EChartsCoreOption {
  const times = points.map((point) => formatHourMinute(point.snapshotTime))
  const online = points.map((point) => point.onlineCount)
  const totals = points.map((point) => point.totalCount)
  const maxValue = Math.max(1, ...totals)
  const labelInterval = times.length > 8 ? Math.ceil(times.length / 8) - 1 : 0
  const showSymbol = times.length <= 60

  return {
    animationDuration: 300,
    grid: { left: 40, right: 16, top: 12, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(7,29,56,.92)',
      borderWidth: 0,
      textStyle: { color: '#e6f0ff', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: times,
      axisLine: { lineStyle: { color: '#e6ebf1' } },
      axisTick: { show: false },
      axisLabel: { color: '#8492a6', fontSize: 11, interval: labelInterval },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: maxValue,
      splitNumber: Math.min(5, maxValue),
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#8492a6', fontSize: 11 },
      splitLine: { lineStyle: { color: '#e6ebf1' } },
    },
    series: [
      {
        name: '在线客户端',
        type: 'line',
        smooth: false,
        showSymbol,
        symbolSize: 6,
        data: online,
        lineStyle: { color: '#1769c2', width: 2.5 },
        itemStyle: { color: '#ffffff', borderColor: '#1769c2', borderWidth: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(23,105,194,.18)' },
            { offset: 1, color: 'rgba(23,105,194,0)' },
          ]),
        },
      },
      {
        name: '客户端总数',
        type: 'line',
        smooth: false,
        showSymbol: false,
        data: totals,
        lineStyle: { color: '#9ba8b7', width: 1, type: 'dashed' },
      },
    ],
  }
}

function updateChart(points: TrendPoint[]): void {
  chart?.setOption(buildOption(points), true)
}

function onHoursChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value)) emit('update:hours', value)
}

onMounted(() => {
  if (!chartEl.value) return
  chart = echarts.init(chartEl.value)
  updateChart(props.points)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(chartEl.value)
})

watch(() => props.points, updateChart)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = undefined
})

const latestOnline = computed(() => props.points.at(-1)?.onlineCount ?? 0)
</script>

<template>
  <article class="panel">
    <div class="panel-header">
      <div class="section-title">客户端在线趋势</div>
      <div class="legend">
        <span><i />在线客户端</span>
        <span><i class="total" />客户端总数</span>
        <span v-if="points.length">当前 {{ latestOnline }} 台在线</span>
        <select class="control legend-select" :value="hours" aria-label="趋势时间范围" @change="onHoursChange">
          <option v-for="option in hoursOptions" :key="option" :value="option">近 {{ option }} 小时</option>
        </select>
      </div>
    </div>
    <div class="chart-body">
      <div ref="chartEl" class="chart-canvas" />
      <div v-if="!points.length" class="chart-empty">暂无趋势数据</div>
    </div>
  </article>
</template>

<style scoped>
.legend-select {
  height: 28px;
  padding: 0 6px;
  font-size: 12px;
}
</style>
