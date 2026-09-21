'use strict'
import Chart from 'chart.js/auto'

function listToDict (list) {
  // gpt
  return list.reduce((acc, item) => {
    acc[item.date] = item.count
    return acc
  }, {})
}

function toDateString (dateObj) {
  return dateObj.toISOString().split('T')[0]
}

function getTimeSeriesData (start, end, data) {
  const dataDict = listToDict(data)
  const chartData = []
  const current = new Date(start)
  while (current <= end) {
    const curString = toDateString(current)
    chartData.push({
      x: curString,
      y: dataDict[curString] || 0
    })
    current.setDate(current.getDate() + 1)
  }
  return chartData
}

// Keyed by canvas id, not element: htmx swaps the #charts fragment, so the previous
// chart sits on a detached canvas that Chart.getChart can no longer reach.
const chartsByCanvasId = new Map()

export const barChartWithDates = (ctx, start, end, data, label) => {
  const canvas = ctx.canvas
  chartsByCanvasId.get(canvas.id)?.destroy()
  // Chart.js refuses to build a second chart on a canvas that still has a live one.
  Chart.getChart(canvas)?.destroy()
  const chartData = getTimeSeriesData(start, end, data)
  const chart = new Chart(ctx, {
    type: 'bar',
    data: {
      datasets: [
        {
          label,
          data: chartData
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: false
        }
      },
      scales: {
        x: {
          title: {
            display: true,
            text: 'Date'
          }
        },
        y: {
          beginAtZero: true,
          title: {
            display: true,
            text: label
          }
        }
      }
    }
  })
  chartsByCanvasId.set(canvas.id, chart)
  return chart
}
