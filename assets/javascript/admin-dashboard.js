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

export const barChartWithDates = (ctx, start, end, data, label) => {
  const chartData = getTimeSeriesData(start, end, data)
  return new Chart(ctx, {
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
}

export const cumulativeChartWithDates = (
  ctx,
  start,
  end,
  data,
  label,
  startValue
) => {
  const chartData = getTimeSeriesData(start, end, data)
  let currentValue = startValue || 0
  for (const row of chartData) {
    currentValue += row.y
    row.y = currentValue
  }
  return new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        {
          label,
          fill: true,
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
}

// Backward compatibility shim (TODO: Remove after Phase 6)
window.SiteJS = window.SiteJS || {};
window.SiteJS.adminDashboard = {
  barChartWithDates,
  cumulativeChartWithDates
};
